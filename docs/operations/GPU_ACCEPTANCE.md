# Maestro Deferred GPU Acceptance

This is the single GPU-required acceptance ledger for the current product
waves. The underlying source, model-free tests, UI tests, and build checks may
already be complete; do not describe them as blocked merely because no GPU was
available. Close a row only with the live GPU/runtime or generated-output
evidence named here.

The owner has authorized activating every CPU-safe account, support, sharing,
implemented supporter-benefit, credit, and queue surface now. The implemented
default benefits are supporter recognition and bounded queue priority;
promotional Maestro credits are the separate hosted allowance. GPU absence
delays only model/runtime and real-demand acceptance. It is not a reason to
leave the credit system or other CPU-safe product surfaces disabled.

## Common gate

Before a GPU row:

1. Confirm the GPU is available to Maestro and not reserved by higher-priority
   owner or agent work. Use the current runtime and current dynamically resolved
   loopback URL; never reuse an old port or readiness record.
2. Confirm accounts and the zero-quarantine project migration are active, the
   owner is signed in, and the intended test project is authorized. Do not turn
   accounts off to make a legacy benchmark client work.
3. Read the existing H3 legal-access record. Japan (`JP`) is the owner's current
   declaration; do not request another declaration when the live signed record
   is current.
4. Keep prompts, project names, account IDs, URLs, cookies, model paths, and
   credentials out of tracked reports. A report may retain case ID, model and
   asset revisions, settings, seed, timings, memory measurements, output digest,
   and the evidence label.
5. Run one case at a time. Stop after a load, compatibility, CUDA, OOM, output,
   or cancellation failure; preserve the failed row instead of silently routing
   to a different model or attention backend.

Where the current loopback benchmark runner has an authorized project-access
path, named H3 cases use:

```bash
python app/scripts/benchmark_h3_profiles.py \
  --base-url "$READY_URL" \
  --project "$TEST_PROJECT" \
  --case CASE_ID \
  --output-dir "$UNTRACKED_REPORT_DIR"
```

Keep the variable values outside tracked files. If active account membership
cannot be presented to this runner, execute the same named model/profile through
the signed-in owner UI; do not weaken account enforcement or substitute a static
`--dry-run` result.

## Acceptance matrix

| Area | Live action | Accept only when |
| --- | --- | --- |
| H3 Base FL2VA and managed assets | Run `base_native_sdpa`, `base_turbo_4_sdpa`, and `base_turbo_8_sdpa`. Then run `base_high_native_sol` explicitly. | Each exact managed checkpoint, conditioner, VAE, audio asset, and Turbo asset resolves from its declared revision; the selected case completes without fallback; video and synchronized audio are finite, playable, correctly timed, and visibly match the synthetic brief. High must use the requested Sol settings rather than a hidden dense fallback. |
| H3 Ref2VA | Run `ref2va_native_sdpa`, `ref2va_turbo_4_sdpa`, and `ref2va_turbo_8_sdpa` with the runner's procedural reference. | The Ref2VA checkpoint—not the FL2VA alias—is loaded; ordered reference identity is visible in the result; video/audio complete; exported frames show the red-circle/yellow-triangle identity; no request is silently rerouted to Base FL2VA. |
| Dasiwa Ref2VA Hybrid V1 | Select the visibly provisional installed-base profile, use the fixed procedural reference, and run one 4-step 608x352 SDPA clip with only the Dasiwa LoRA at strength 1.0. | Runtime admission binds the installed Ref2VA checkpoint and exact LoRA bytes; no managed Turbo, Spectrum, LightX2V, SLA, MATLOW, or other LoRA is stacked; the 124-frame A/V output is playable and visibly preserves reference identity. A coherent result on the suspected-compatible base does not certify the author's exact-base contract. |
| H3 Better Motion Ref2VA V1 | With the same fixed reference and seed, run one 28-step 1344x768 Sol-Attn clip with only Better Motion V1 at strength 0.9. | Exact Ref2VA and LoRA identity is retained with no fallback or stacking; video/audio complete; motion and reference adherence are reviewed against the Dasiwa probe. A neutral coherence sample proves runtime compatibility, not mature-content quality across prompts. |
| H3 W4A8 | First run the runtime's `python scripts/validate_h3_w4a8.py` from its installed environment, then run `w4a8_turbo_8_sdpa`. | The finite-output marker is bound to the current GPU, Torch, Triton, runtime revision, and checkpoint; the full generated sample is playable and visually acceptable; no dequantization or different checkpoint fallback is reported. |
| H3 PinkCherry FL2VA | Select **PinkCherry H3 FL2VA (Explicit)** in the signed-in owner UI and generate one minimum-duration private sample. An allocation-only scout may use `pinkcherry_high_allocation_p4`, but it cannot close this row. | The exact PinkCherry checkpoint and required assets load, the explicit model remains the selected producer, audio/video complete, and the owner accepts the output. A two-step allocation probe or a Base fallback is insufficient. |
| H3 aliases and linked assets | For each Base, Ref2VA, W4A8, and PinkCherry row, capture the canonical model ID plus the resolved asset revision/name from the live model/download projection before and after generation. | Both supported linked-install aliases resolve to the same intended canonical bytes where declared, stale partial assets are not accepted, every variant keeps its own checkpoint identity, and deleting/rechecking one alias cannot make an unrelated or incomplete file appear ready. |
| H3 Director | Create one short Director project using H3 Base and one using the intended reference-conditioned path. | Preview, approval, queue handoff, clip generation, audio, final join, saved pipeline state, and rerun/recovery all retain the selected H3 model/profile, exact dialogue, reference labels, timing, and project authorization. Human review accepts the final, not merely the clip plan. |
| FlashVSR delivery | Run `base_1080p_delivery`, `base_ultra_delivery`, and the exact 4K gate with `python app/scripts/benchmark_h3_profiles.py --base-url "$READY_URL" --project "$TEST_PROJECT" --live-4k-acceptance --output-dir "$UNTRACKED_REPORT_DIR"`. | The native H3 result is preserved, FlashVSR is the recorded delivery stage, output geometry is exactly 1920x1080, 2688x1536, and 3840x2160 respectively, audio remains aligned, final publication selects only the explicit delivery output, and the owner accepts detail/identity without a mislabeled native-4K claim. |
| Official H3 SageAttention2++ | Run `base_native_sage2`, then the same-seed `base_fast_864_turbo_8_sdpa` / `base_fast_864_turbo_8_sage2` pair. | The pinned source build and validation record bind to the current GPU/Torch/CUDA/Triton/checkpoint; kernel execution is proven with no fallback; video and audio are both reviewed; any speed statement separates cold load from generation. Do not promote Sage to W4A8, Ref2VA, or PinkCherry from Base-only evidence. |
| Sol runtime | From the installed Sol environment run `python scripts/verify_sol_runtime.py`, then complete `base_exact_dense_sol` and `base_high_native_sol`. | Verification reports the exact supported CUDA capability and required Python/PyTorch/CUDA/Triton versions; generated cases record `sol_attn`; output is finite with synchronized audio and no silent SDPA/Sage fallback. Compatibility aliases `start_sol.js` and `sol_install.js` must still enter the canonical start/update flows rather than a separate runtime. |
| Optional FlashAttention | Run Maestro's normal startup preflight with the installed optional FlashAttention wheel, then exercise one feature that selects it and one forced incompatibility/fallback check. | The current wheel imports and executes on the actual GPU/runtime when compatible. When incompatible or broken, Maestro disables it once, reports the bounded fallback, and successfully uses SageAttention/SDPA without poisoning later imports. Import success alone is not kernel acceptance. |
| Music3 Studio generation | After GPU clearance, obtain a fresh exact coordinator lease; review the pinned MiniMax host term, fetch the native WanGP checkpoint manifest, and generate one private lyrical song plus one instrumental song in Studio. Repeat a bounded cancellation and restart/recovery case. | The official license and optimized asset revisions match the approved native recipe; the normal project queue records playable audio and provenance, requested duration/style and lyric sections hold, cancellation/recovery do not duplicate or lose results, and the owner accepts musical and vocal quality. Confirm authorized local, LAN, and stable Cloudflare parity. Source checks or a listening server without a song do not close this row. |
| Music3 to Director | Use **Send to Director** from an edited Lyric Playground song, generate the soundtrack, approve it, and continue through one short Director output. | Director receives the exact workspace, model, style, lyrics, duration, and instrumental state; stale preparation is rejected after an edit; the accepted song's measured structure drives the video plan; the final audio/video is aligned and accepted by the owner. |
| Scene Kit to Generate | Build a Character/Location selection from at least two kept variants with known output IDs and apply it to a reference-capable generation. | The submitted job contains the exact ordered output IDs, paths, labels, and asset/variant identities selected; no path-only recovered row is accepted; the generated output visibly follows both references; changing project/account/asset state during staging cannot commit a partial set. |
| Scene Kit to Director | Apply a Cast Board/Scene Kit selection, create a Director preview, then generate and rerun one clip. | Preview, queued clip, saved project, final join, and rerun preserve the exact selected output IDs and labels; project/account epoch changes fail atomically; the final clip visibly retains the intended cast and setting. UI attachment alone does not close the row. |
| Krea owner generation | While signed in as the owner, record the current Krea 2 owner declaration only when the selected profile/runtime is ready, then run one private Krea 2 generation and one Quad Krea Character Sheet. | The server resolves role scope to `noncommercial`; no client-selected scope can override it; the exact Krea profile and license revision remain attached; both outputs complete and the owner accepts identity/layout quality. |
| Krea member generation | With a non-owner user account, run one authorized Krea 2 generation in a project where that member can generate. | The server resolves role scope to `commercial_under_1m`; project membership and generation permission are enforced; no owner/noncommercial scope leaks to the member; the output completes under the selected Krea profile. |
| Dynamic Krea experimental | Explicitly select **Dynamic — Krea 2 (experimental)** and generate one Character Sheet after the standard Quad rows pass. | It remains visibly experimental and opt-in, never replaces Quad FLUX as the safe default, preserves output/repair lineage, and passes owner visual review. Failure does not block the standard Krea/FLUX sheet path. |
| Credit queue under real GPU demand | With hosted enforcement live, enqueue otherwise-valid jobs representing funded, partial/zero, and owner-exempt accounts while a real GPU job occupies the worker; include hold/resume and a restart/recovery cycle. | All submissions become durable jobs; funded work receives only bounded priority; partial/zero allowance remains in the lowest ordinary FIFO band and eventually receives starvation-bound capacity; the owner and local/authenticated-LAN execution consume no allowance; reservations conserve units across consume, release, cancellation, restart, and recovery; no path returns a flat credit `402`. |

## Closure record

For each row record: date, source revision, host platform, GPU/runtime identity,
model and asset revisions, case/profile, output digest, pass/fail, evidence class,
and residual gap. Keep the actual media in the project's normal private output
store, not in Git. A model-free test, downloaded asset, runtime marker, printed
URL, completed queue plan, or screenshot of controls is not generated-output or
human acceptance.

### 2026-09-21 general generation recovery, rechecked 2026-09-23

The existing private live-matrix project contains completed generated files,
rechecked against their sidecar `producer_media_sha256` values. One FLUX.2
Klein 9B image is a decodable 672x672 PNG (SHA-256
`d04359472788c634da4a63f0ea17adef72ba8c3fb4ee950b163cea860faa3f55`).
One H3 Base short video is a 5.167-second 608x352 HEVC/AAC file (SHA-256
`c6630eff5a113063ec743c17b0a29f19fabc0cd8c97211ac29ddd7313ed75282`);
the joined long-form result is 16.167 seconds at 608x352 with H.264/AAC
(SHA-256
`ebd0093951d7bcaf45ac7d2b7cd0bea04242581d2d9c7cf16d20b91b1bcb3a4a`).
One ACE-Step Turbo music output is a 120-second, stereo 48 kHz PCM WAV
(SHA-256
`6f932a872811de7ded0da481527529474c776d3eaf5f93ce240b02beed9c9c2f`);
an audio-level probe found mean -11.7 dBFS and peak 0.0 dBFS, so it is not
silent, though this does not judge musical quality.
The signed-in stable Cloudflare UI also displayed two completed local chat
replies to exact-match synthetic prompts in the same project. The image and a
frame from the long-form video were visually inspected during the recheck.
This is retained generated-artifact and browser evidence for the earlier
recovery wave, not a fresh generation on the current Music3 revision or owner
acceptance of quality. The original release revision is not recorded in these
sidecars, so do not infer an exact source binding from this retrospective check.

### 2026-09-23 fresh signed-in image generation

With the exact `maestro-local` coordinator grant validated before submission and
continuously checked during the job, the signed-in stable Cloudflare UI queued
one FLUX.2 Klein 9B Studio image on the running `f432921` service revision.
The normal queue changed from waiting to running to clear, and the project item
count increased by one. The four-step, 672x672 RGB PNG completed in 89 seconds;
its SHA-256 is
`65872c808bbc34fda31f06af967795181b42dfd44aecaca1707dd8a645a903a7`,
matching the sidecar `producer_media_sha256`. Direct inspection showed the
requested two objects and no visible text. This is fresh live GPU, stable-share
browser, generated-artifact, and agent visual evidence for this single image
case; it does not accept other models, Music3, or owner quality.
In the same signed-in browser, a fresh local Gemma 4 31B Chat request loaded
the selected model and returned the exact synthetic response requested. This
is live stable-share Chat evidence, separate from the image and earlier
retained responses; it does not establish other LLM/provider behavior.

A separate signed-in Z-Image Turbo 6B Studio request initially completed all
eight denoising steps but failed at VAE decode. The installed VAE loaded in
FP32 while the pipeline passed BF16 latents; a direct component reproduction
raised a convolution input/bias dtype error, and decoding succeeded when the
latent was cast to the VAE dtype. After the narrow pipeline fix and a
coordinated Pinokio restart, a fresh 848x480 RGB PNG completed in 14 seconds
through the stable-share queue on RTX 5090 with `app/env-rtx50` (Torch
2.10.0+cu130); those source bytes were published as `05207df`. The main
`ZImageTurbo_quanto_bf16_int8.safetensors` and
`ZImageTurbo_VAE_bf16.safetensors` SHA-256 values are respectively
`dd4b9172c0c4d69aa6d03a61f5be56c5fee5a9bc4ce1fcca5fc8633cec78d850` and
`f5b59a26851551b67ae1fe58d32e76486e1e812def4696a4bea97f16604d40a3`;
the VAE and scheduler config digests are respectively
`e80af1e64a71883a9d10c3159d2e493e5934508da57852f6a180ae6ae63b14bd` and
`3b979ab0956e4f5e8d02ec409ac6a4ece1555191d15bd10788fdc85fea5d13fc`.
The output SHA-256 is
`e70c767867b83405fb1bc3ec8f34cdd3e1ba1a96b9dfc72acbd2cab672d4f59c`,
matching the sidecar `producer_media_sha256`; the Gallery displayed the new
output. Agent inspection found a recognizable red robot on the requested pale
blue setting, though its chest emblem was rectangular rather than triangular.
This accepts this Z-Image execution and decode path, not exact prompt fidelity
or owner quality. After the failed job, the idle generation-model release API
also returned success and cleared its loaded flag while local and stable
`/ready` remained healthy.

A fresh ACE-Step v1.5 Turbo LM_4B Studio music job also completed through the
signed-in stable-share queue. The saved stereo 48 kHz PCM WAV has SHA-256
`f778081be934a1d80302727e7c98e11d7104aabd94772d7b0ffbc122a501e391`,
matching its sidecar, and measurable audio (mean -15.0 dBFS, peak 0.0 dBFS).
The Advanced Settings control visibly showed a 30-second request, but the
sidecar records `duration_seconds: 120` and the output measures exactly 120
seconds. This is a live duration mismatch on the pre-deployment `f432921`
service; it must be diagnosed and retested before duration control is accepted.
No owner listening judgment is implied.

After deploying the exact-seconds audio control in `2ee121e`, a second
signed-in ACE-Step v1.5 Turbo LM_4B request selected 30 seconds. Its sealed
request and published sidecar both record `duration_seconds: 30`; the stereo
48 kHz PCM WAV measures exactly 30.000 seconds. Its SHA-256 is
`9ae6f8adc2d72c4fc6bf0f43b1d741db3627266fc717fd9b6b695d56c98e47d2`,
matching the sidecar. The audio is non-silent (mean -13.5 dBFS, peak 0.0 dBFS).
This closes the observed duration-submission mismatch for this model and
request, without implying listening acceptance or other-model coverage.

A fresh MiniMax H3 Base FL2VA **Quality** Studio job then completed on the same
pre-deployment service, with 23 steps at 960x544. The 5.167-second HEVC/AAC
file has SHA-256
`d06891432e2b712e1c75c2c74dd22c6a9c12f768bb4e4b016228e3c5a0336273`,
matching its sidecar. The stereo 32 kHz audio is non-silent (mean -16.0 dBFS,
peak -1.9 dBFS). Frames at 0.5, 2, and 4 seconds showed the requested blue
pinwheel with a changed blade orientation. This accepts one short Base
generation path technically and visually; long-form continuity, reference
conditioning, other profiles, and owner quality remain separate rows.

A second signed-in H3 Base FL2VA Quality request used a 16.17-second authored
two-beat timeline. Both planned segments completed, and the final 960x544,
24 fps H.264/AAC file measures 16.167 seconds. Its SHA-256 is
`5277f36208a392bfc572071d614e469e340f28f7dfc32726b20e257e99149914`,
matching the sidecar. Stereo 32 kHz audio is non-silent (mean -18.9 dBFS,
peak -1.4 dBFS). Sampled frames before and after the 8-second join retain
the same pinwheel, table, and room, with the requested color change in the
second beat. This accepts one >15-second two-segment path technically and by
agent visual inspection. During staging, the server logged `Object of type
Image is not JSON serializable` while embedding MP4 metadata. The final media
and sidecar were initially published; embedded metadata reliability remains
open. On the next coordinated restart, recovery moved this job's media and
sidecars into project-local private quarantine and held the queue entry with
`final_output_recovery_incomplete`. The bytes and hashes were retained, but
first-restart Gallery publication and recovery are **not yet accepted**.
On a second quiet, coordinated restart, the existing final-adoption path
verified and restored the final file and sidecar; the signed-in stable-share
Gallery showed 13 items and the queue was clear. The component clips remain
in private quarantine, so clip-level reuse, rejoin, and first-restart finality
remain open; the second restart is a recovery receipt, not a fix for that
first-restart regression.

A new signed-in H3 Base two-segment request then completed after the MP4
metadata repair. The 960x544 H.264/AAC final measures 16.167 seconds and has
SHA-256 `994f9e6d959c3f15d60e188f5f55772d296c68202bce722f52a41bdaf9ac2857`,
matching its sidecar. Embedded metadata parses; the audio is non-silent (mean
-19.2 dBFS, peak -1.8 dBFS). Sampled frames retain one red pinwheel across
the join, though the later blades drift toward extra colors. On its first
coordinated restart, the final was adopted and the queue cleared, but its
two source clips were quarantined and the Gallery lost its rejoin action.
After deploying guarded component-closure adoption and restarting, the final
and both source clips and sidecars were public with unchanged hashes; the
signed-in stable-share Gallery showed 15 finished items and **Rejoin all 2
clips**. The component clip SHA-256 values are
`8719bde546eee64ed0e6a8e48f9f44b3190cc4e439b3ca21c5dd71af8043d56a`
and `5a3d1b6d785c1fda6e4157731ae30a0f2798a50237aa1caf2e6250effc371edc`.
This proves recovery of an existing output under the deployed fix; the next
request supplies the separate first-restart check. Owner visual acceptance
still requires separate evidence.

A fresh post-fix H3 Base FL2VA Quality request completed as two authored
segments and an 18.167-second 960x544 H.264/AAC final. The final SHA-256 is
`5fdc3f93cc6607c32209ab3510d675b5e58c38fd24594ace0eace29b16f5ab39`;
the source clip hashes are
`abd9190949864fea2910a3228dcb71016311eaaa4dbd06766d0d02d66c23e057`
and `7f237e30385ccc3474fb223c5c988349d6690497e2fd44ade6e270e529fbd6a2`.
All three match their sidecars. The final has non-silent audio (mean -18.2
dBFS, peak -2.5 dBFS). On its first coordinated restart, the same files and
hashes remained public, the signed-in stable-share Gallery showed 17 finished
items and **Rejoin all 2 clips**, the queue was idle, and stable `/ready`
returned 200. This accepts first-restart final and component durability for
this request. Sampled frames exposed a separate quality failure: the second
shot introduced a person holding a smaller pinwheel instead of preserving
the tabletop subject. Owner visual acceptance remains open.

Gallery Regenerate on the earlier long-form final submitted an ordinary
8-second repeat of the final segment's seam prompt. Its output was valid but
unrelated to the full-video brief. The UI restore path now recovers the
original full prompt and frame count from long-form metadata when no original
media references are required; older reference-conditioned outputs instead
ask for references to be reattached. A signed-in Gallery **Regenerate** after
this change sealed the original 307-character whole-video brief and 436-frame
request, then completed two H3 segments and an 18.167-second 960x544 H.264/AAC
final. The final SHA-256 is
`f2628ef2b457ea44eda270dd453696e9fab656863636a7a63b9385e2287081e0`;
component SHA-256 values are
`8c37499249e482d01cba307daea3b7ba4866317fb803b164a9e668203288c174`
and `d3b4313be6e157c2620fec99f70fe0658b67d2ec0b5cc1b333a9ae0bbd9cb8f4`.
All three match their sidecars. The final audio is non-silent (mean -18.2 dBFS,
peak -2.5 dBFS); the stable-share Gallery shows the final, both related clips,
and **Rejoin all 2 clips**, with the queue idle. This accepts long-form reroll
submission and completion technically. Sampled frames expose the same separate
quality failure as the source run: a person holding a smaller pinwheel replaces
the tabletop subject in the second shot. Owner visual acceptance remains open.

A separate signed-in Gallery **Regenerate** after the inline H3 timeline fix
(`b2adc7b`) submitted that same 307-character, 436-frame brief on the fresh
Pinokio service through stable Cloudflare. Its two same-line bracketed ranges
were planned separately; the saved second-segment prompt now explicitly says
to continue the same red four-blade pinwheel on the tabletop with the camera
arc. The runtime extracted the first clip's last frame for the continuation.
Job `8aaa877042294a0e9e3ccb9b920182ce` completed both 960x544 HEVC/AAC
components and an 18.167-second 960x544 H.264/AAC final. First, second, and
final SHA-256 values are respectively
`5d469008d09492c1893ceabd48c3ad50e1a427964430cabc0cadfae7980753bd`,
`a1a0c5fde6135f68ac859a1b5129d2a2ea5b64ad6589e3847997db87cea2ed42`,
and `a1feb8aeff3c3dae82b8a5451dafa2ac1b43a1d46c243c63e6dc6376cc12d8d0`;
all match their sidecars. The final's audio is non-silent (mean -24.7 dBFS,
peak -9.0 dBFS). The signed-in stable Gallery showed the finished final and
**Rejoin all 2 clips**, with the queue idle. Agent-inspected frames at 2, 8,
9.5, 12, and 16 seconds retain the red pinwheel on the wooden tabletop; the
previous person/hand substitution is absent in these samples. This accepts
this execution and sampled visual-continuity path, not unsampled frames,
post-generation restart durability, or owner listening/visual acceptance.

A signed-in **Tools → Upscale → FlashVSR 2x** request on the existing 5.167-second
608x352 H3 teapot clip completed in 70 seconds under the same validated Maestro
lease. The public result is a 5.184-second 1216x704 HEVC/AAC MP4, SHA-256
`cc98f092b49c45e8474fb9571f80cd1566002d89c46b915ec8bb1bf0252b8b57`,
matching its sidecar. A sampled frame retains the teapot and floral details at
twice the source dimensions. Decoded mono audio has 0.9995 approximate
normalized correlation with the source after a ~32 ms offset, supporting
preservation through remux/encoding. The signed-in Gallery rose to 19 finished
items and the queue returned idle. This accepts one live FlashVSR2x output;
other upscale variants, cancellation, crash adoption, and owner detail
acceptance remain open.

A signed-in **Tools → Revoice → Single Voice** request used a 5.167-second
synthetic H3 video with earlier test-song vocals plus a 16-second reference
voice from another generated take. The UI uploaded both inputs, the worker
separated vocals, downloaded its first-use SeedVC assets, converted the vocal,
and remixed it with the background. The terminal result is a private 5.167-second
608x352 HEVC/AAC MP4, SHA-256
`14fbc84f518fb8668efb0f9b8f92b62e7d887f16c137a4efa8bd8a99e0515645`,
matching its sidecar. Its encoded video stream is byte-identical to the input;
the non-silent mono output audio differs substantially from the original
(best approximate normalized correlation 0.185 over ±100 ms). The Gallery
shows the result with its private preview blurred and the queue returned idle.
This accepts one live SeedVC tool execution and video preservation technically;
the 48 kHz stereo source became 44.1 kHz mono, so stereo/background fidelity,
speaker similarity, two-voice mode, cancellation, crash
adoption, and owner listening remain open. After the first-use downloads the
root filesystem had about 1 GB free, so additional model installations need
an explicit storage check before download.

The first coordinated Pinokio **Restart Maestro** after these three requests
returned a new local ready URL and stable Cloudflare `/health` and `/ready` 200.
The signed-in project Gallery again listed 20 finished items, including the
reroll, FlashVSR, and private Revoice results; the queue reported zero active
jobs. Those three MP4s kept their pre-restart SHA-256 values and matching
sidecars. This accepts first-restart durability of these successful outputs,
but does not simulate a crash during an in-flight tool job.

The stereo follow-up ran under a fresh coherent six-hour Maestro coordinator
grant (`maestro-seedvc-stereo-live-01a0a212-20260923`) with a bounded lease
guard. A coordinated Pinokio restart loaded the channel-preserving demux/remix
change; the new local `/ready` and stable Cloudflare `/ready` both returned 200.
The same signed-in **Tools → Revoice → Single Voice** inputs then completed as
job `62735095e68a4387848e72babf6a3e2b`. Its private 5.167-second 608x352
HEVC/AAC result has SHA-256
`39f0f3a3b6f2e4b9d2bbf96b706548367ca886035401cf2168c1f0861bdc7345`,
matching the sidecar. The video stream is byte-identical to the source. Audio
is now **44.1 kHz stereo** rather than mono: left/right difference RMS is
0.0568 versus 0.0965 in the 48 kHz stereo source, with 0.874 correlation
between the decoded channel-difference signals. Output audio differs from the
source (0.227 RMS sample difference), so this is not a passthrough or duplicate
channel claim. The Gallery rose to 21 finished items and the queue returned
idle. This accepts channel-layout and background-spatial preservation for one
single-voice live path; speaker similarity, two-voice mode, cancellation,
in-flight crash recovery, and owner listening remain open.

The signed-in stable-share **Tools → Upscale → FlashVSR 3x** request on the same
5.167-second 608x352 synthetic source completed as job
`b3c4062d7166436cbc12cd60a7de2aa4`. Its 5.184-second 1824x1056 HEVC
result retains 48 kHz stereo AAC, and SHA-256
`27612b984775e2f51952af047e6e1258d936588fa1d481e8d403d60bb869fac4`
matches the sidecar. A sampled frame retains the teapot and floral detail.
The Gallery rose to 23 items and the queue returned idle. A separate **4x**
request completed as job `da8bf23925374809aaf13e048ddcc6c9` in 166
seconds. Its 5.184-second 2432x1408 HEVC result retains 48 kHz stereo AAC;
the 18,351,378-byte file's SHA-256
`37ddb2ffceb77b9c12596d712e4060fa394224e7cf9e3315ad32d076fbfd891c`
matches the sidecar. Its sampled frame preserves the subject and details.
The encoded audio streams of the 3x and 4x outputs are identical. Compared
with the uploaded source after decode and a 21.25 ms offset, each has about
0.963 normalized mono correlation. The Gallery rose to 24 items, the queue
returned idle, and stable Cloudflare `/ready` returned 200. These are live
single-pass 3x and 4x technical completions, not owner detail acceptance or
the separate two-pass and H3 delivery-profile matrix.

For cancellation, the signed-in UI stopped one FlashVSR2x request during
Caching and another after it reached Denoising step 2/14. In each case the
queue returned idle, the Gallery stayed at 22 items, no new upscale artifact
appeared, and stable `/ready` remained 200. This accepts the visible running
job Stop path for these two probes. It does not prove an in-flight process
crash can be adopted or resumed. An attempted Revoice Stop was too late: that
job completed before the action, so it is not counted as cancellation evidence.

The signed-in stable-share YuE2 composer also ran its local guide-backed
Gemma 4 31B drafting path. Its initial live request failed because the model
omitted the native Vocal and Ins score voices. After adding the native ABC
contract and structured JSON response, the same brief populated style,
lyrics, and score. The installed YuE2 native parser accepted the 24-bar,
two-voice score at 88 BPM (nominal 65.45 seconds). The generated lyric draft
was too dense for its 93 Vocal notes, so musical/prosodic alignment and actual
YuE2 audio generation are not accepted from this draft. The composer prompt
now explicitly co-designs lyric syllables and score note slots; a fresh live
retest is required after that change reaches the running service.

After deployment, the same signed-in local composer returned a structurally
valid 24-bar, two-voice ABC score with 96 Vocal notes, but paired it with 238
English lyric words. That is still far too dense for the melody. A conservative
English word-count warning now flags such a mismatch beside the score while
leaving the artist free to revise it. A manually tightened 86-word lyric kept
the six score sections aligned for the first fresh YuE2 synthesis check.
Under a separate validated Sound/Vision GPU lease, the signed-in stable-share
UI submitted and completed that native, no-LoRA YuE2 take. The 48 kHz stereo
float WAV measures 87.999 seconds and has SHA-256
`4832190159eab365407b96274d1bcfb49698dfd2f90f62375a687f417b8020e8`.
Its mean level is -16.6 dBFS and peak -0.4 dBFS; the final five seconds have
mean level -22.8 dBFS. The delivery receipt reports no warnings and neither
ABC nor semantic truncation. The signed-in project library shows the complete
take with an audio player and WAV download. This accepts one live native YuE2 audio
path technically; lyric intelligibility, musical quality, and owner listening
remain unaccepted.

A second signed-in take selected the installed DreamPop v2 style LoRA at
strength 1.0. The request and delivery receipts record the exact checkpoint
ID `ddee049784269e6b6298ce2d605b1bff` and SHA-256
`617852d379c66b0407879421a353233d29cda8188cca82973a94c61b512ca03d`;
the native worker reports 224 adapter modules. This LoRA selects no ABC
planning, so the take does not test score alignment. Its stereo 48 kHz WAV
measures 213.839 seconds, SHA-256
`c70beceea3ce6ffeafdbf0908db232c5827dd3d4226d89f22a06b3b318037d75`,
with mean level -16.0 dBFS and peak -0.6 dBFS. The receipt has no warnings
or generation-limit flags, and the project library shows an audio player and WAV
download. This accepts one exact native LoRA synthesis path technically;
owner listening and LoRA quality comparison remain open. The Sound/Vision
lease was withdrawn after both takes reached terminal success.

After the one-pass local density revision was deployed, a fresh signed-in
stable-share composer request for a baker opening at dawn produced 46 sung
English words for 72 Vocal notes across 20 bars at 88 BPM. The native
two-voice score passed the YuE2 plan-first review, and the same project UI
continued take `c9ee3ab8ae4643e988a7dcf499bbe97b` to terminal success.
The downloadable float WAV is stereo 48 kHz, 55.119 seconds, SHA-256
`c60bfac7f2d4ca2d8ad84e96be2a8175a1702aa9e0d0fab66da9774e08517c31`.
The delivery receipt reports zero clipped samples, no warnings, and neither
ABC nor semantic truncation. The last five seconds have mean level
-24.47 dBFS. This accepts a complete guide-backed composer-to-native-YuE2
path technically; lyric intelligibility, musical quality, and owner listening
remain open.

A second signed-in stable-share check used a manually authored eight-bar,
plan-first YuE2 score after correcting the UI review-marker race. Take
`e70cc6e3f2b74e24891dc8175eb1386e` passed score review, continued, and
succeeded with no stale review warning visible during rendering or after
completion. Its 48 kHz stereo float WAV measures 24.759 seconds and has
SHA-256 `99f48676d40c3ad76a3f3728986c31a4837fceb11135de9edfd89f419bb9691b`.
The receipt records no clipping, warnings, or ABC/semantic truncation. This
is live acceptance of that review UI transition and short native render,
not owner listening acceptance.

### 2026-09-23 YuE2 combined adapters and instrumental ABC

A fresh `sound-vision-local` coordinator grant was validated before each
submission and observed during both jobs. The signed-in stable-share UI used
the installed YuE2-3B native worker at Sound/Vision source revision
`92a73cc7652fcc1f937855e4b765e0a0edd7ff2e`, model revision
`1a96eca688d6ae5d7f0feb88573fec89920fcd19`, and VAE revision
`95535e72a97bc0f09b8ada125d26b4009428c0e8`.

The combined-adapter vocal case selected the artist step-950 checkpoint
`e461611cd0e9d424b1dcb163b7460ba133681e6d55d3d0156a59039615c6235f`
and DreamPop v2 step-1000 checkpoint
`617852d379c66b0407879421a353233d29cda8188cca82973a94c61b512ca03d`,
each at strength 1.0. The native delivery receipt records both AR/NAR adapters,
448 adapter modules and 392 updated model weights. Its 48 kHz stereo float WAV
has SHA-256 `b8e971dadfbb06aeaa7d7e9a1635cf54c470e452cc0893fb558e79ff78455851`
and measures 359.999 seconds; peak amplitude is 0.772 with no clipped samples.
The semantic stage reached its 9,000-token limit and the delivery receipt marks
semantic truncation. The project library correctly warns that the ending may
be incomplete. This proves both adapters executed together and produced audio,
but does not accept song finality or quality.

The no-LoRA instrumental case supplied a manual eight-bar native two-voice ABC
score at 100 BPM, with `[Instrumental]` lyrics and no review pause. The worker
used the external ABC prefix without an ABC-generation step. Its 48 kHz stereo
float WAV measures 19.879 seconds and has SHA-256
`1043dc2672080b1e52c0531511b042450fbd28ee125b48782e64ae9b743c714b`.
Peak amplitude is 0.596 with no clipped samples, no warnings, and neither ABC
nor semantic truncation. The signed-in project library displayed both new takes
with players and WAV downloads. This accepts a short instrumental score-to-audio
path technically; audible absence of vocals, musical quality, and owner
listening remain separate. The unused lease time was withdrawn and its ledger
entry is cancelled.

Clicking Play on the short take crashed the Codex in-app browser tab. The
kernel recorded a ChatGPT-process invalid-opcode trap at that moment; the
Maestro backend logged no audio request, while local and stable `/ready`
remained 200. Playback in that browser is not accepted from these checks.
The saved WAVs and delivery receipts remain intact; reproduce playback in a
separate browser before closing that part of the acceptance row.

### 2026-09-23 YuE2 score-first instrumental adapter and paired sample

The [dedicated YuE2 instrumental adapter](https://huggingface.co/Mothersuperior/YuE2-instrumental-cot-full-loras)
was pinned to source revision `947f2f4b28978b2b6c3e316e6a87925c76bf3c4b`.
Its ComfyUI source checkpoint has SHA-256
`de6a11d5701df103a191c87dc73115266e2420f3834739319dec5c24d2119f31`.
A CPU-only, rename-only conversion mapped 224 LoRA tensors from ComfyUI
`lora_down/up` names to the native `lora_A/B` names. Every tensor was checked
bitwise equal after conversion, and the native loader recognized 112 AR
adapter modules. The installed checkpoint has SHA-256
`e44e108afebcc0bc1158da8c74641b341cf9e88f259c8537bb71bbce916f4c41`.
The model card specifies CC BY-NC 4.0 and `cot=full`; this optional adapter is
installed locally, not made an automatic default.

Under a fresh validated 20-minute `sound-vision-local` grant, a signed-in
Maestro user selected the adapter at strength 1.0, `[Instrumental]`, full
chain of thought, and automatic score generation with the manual ABC field
blank. The native worker applied the 112 AR modules and generated a five-section,
50-bar score. Its 48 kHz stereo float WAV measures 116.079 seconds and has
SHA-256 `d3b4257748743399ca6c315bbda2ca13c1533010a8c5bdfd6fabf3d2a2c020d3`.
There were no warnings or score/semantic truncation flags. The peak reaches
1.0, with a clipped-sample fraction of `0.00002279`; final 0.2-second RMS is
`0.0000837`.

A same-seed, otherwise matching no-LoRA control submitted to the local native
service produced a four-section score and a 96.359-second 48 kHz stereo WAV,
SHA-256 `4df6dbdd4339536ae9309f2f9533cc709c97ec4`. It likewise had no
warnings or truncation flags. Its peak reaches 1.0, clipped-sample fraction
is `0.0000003243`, and final 0.2-second RMS is `0.00433`. Both takes appeared
in the signed-in project My Music library with player and download controls.
This pair proves that the optional adapter executes and changes the native
score/audio path; one seed does not establish musical superiority. Audible
absence of vocals, playback in a separate browser, musical quality, and owner
listening remain open. The grant was withdrawn after both terminal jobs and
its ledger entry is cancelled.

### 2026-09-23 guide-backed instrumental draft and score-to-audio control

After deploying `28a1467`, the signed-in stable-share YuE2 composer loaded the
local 31B writer and selected only `mc-workflow`, `mc-symbolic-score`,
`mc-render-compile`, `mc-arrangement-arch`, `mc-melody`, `mc-harmony`, and
`mc-ai-tell-audit`. It returned `[Instrumental]`, a style caption, and a
two-voice ABC score with SHA-256
`e3b3910d1813d2c77591bc358c04baac2e48d2d612714e3a48c02b25c078c713`.
The score had twelve bars despite an eight-bar request, so that draft did not
accept precise length following. The deployed composer and project review flow
worked; this is live drafting evidence, not listening acceptance.

The signed-in user selected the dedicated score-first adapter at strength 1.0
and submitted that supplied ABC through YuE2's pause/review/continue path.
The take produced a 48 kHz stereo float WAV of 359.999 seconds, SHA-256
`9766f505972cec99283b516d6222eba96d2c76c17d61d3c26afd76682009b4f4`.
Its semantic stage exhausted 9,000 tokens, and the delivery receipt marked
truncation and an incomplete-ending warning. The peak was 1.0 with a
clipped-sample fraction of `0.00014754`. This is a technically delivered file,
but the adapter-plus-supplied-score path did not produce a clean ending.

The same score without a LoRA finished at 36.399 seconds, SHA-256
`3126e8153663e2db0219742cff149e64e74f9f707387eda444e095c486492907`,
with no warnings, truncation, or clipped samples. A further no-LoRA control
using the *same seed* as the adapter take finished at 38.319 seconds, SHA-256
`fa660b1425779ff986472528a246947d14c427b651cb61f4264da227e6c2a772`,
with 959 semantic tokens and likewise no warnings, truncation, or clipping.
The same-seed pair had byte-identical ABC scores, native ABC token arrays,
and semantic prefix arrays; the adapter was the material model difference.
The source model card says this score-first LoRA is intended to write its own
ABC, consistent with its separate successful automatic-score take above.
The UI now warns when a score-first LoRA is selected with supplied ABC and
offers the two useful paths: clear ABC for adapter-led planning, or deselect
the adapter to render the supplied score. Musical quality and owner listening
remain open. Both short coordinator grants were withdrawn after terminal work.

The next signed-in eight-bar instrumental composition did return eight Vocal
and eight Ins bars, but the installed native score parser rejected `F#8` pitch
tokens and then the trailing space after its final barline. No audio was
submitted from that draft. This is a distinct failure from the earlier
twelve-bar length miss: a plausible-looking score did not meet the native
ABC dialect. The composer now prompts for prefix accidentals, trims line-end
whitespace, and uses at most one local correction for invalid sharp suffixes,
explicit numeric total-bar mismatches, or unequal voice lengths. A correction
must keep both voices aligned. The revised path needed a fresh signed-in
native-parser check before it could count as a live repair.

That check passed after the full local gate (5,249 backend tests, 707 UI tests,
type-check and build) and a coordinated Pinokio restart. The signed-in stable
share composed a new guide-backed eight-bar instrumental score with the same
seven `mc-*` guides, `[Instrumental]` lyrics, prefix `^F8` accidentals and no
line-end whitespace. The installed native parser accepted the exact 482-byte
score, SHA-256 `1591b895678a796a8f0d5b7af05fdedfcfd6e0d06c7abf42bfffea1c05b4dca5`:
eight Vocal bars, eight Ins bars, 112 BPM and 17.143 seconds of nominal score
duration. This accepts score syntax and requested bar count for this one draft.

The same signed-in project submitted that exact score to native YuE2 with no
LoRA and no additional score-generation step. The take reached complete and
appeared in My Music with a WAV download. Its 48 kHz stereo file is 44.999
seconds, SHA-256 `f60dd6473065e8fc17091bd361db8687a7448a520506b967795b784bd252073c`;
the receipt reports no warnings, no ABC or semantic truncation, zero clipped
samples and no tail trim. The final 0.2-second RMS is about 0.000038. This
accepts one composer-to-native-audio execution, not audible quality or exact
audio duration: the rendered file is much longer than the score's nominal
17.143 seconds. Both separate 20-minute coordinator grants were withdrawn
after the local writer unloaded and the native runner exited. Local and stable
`/health` and `/ready` returned 200 after the restart.

### 2026-09-23 fresh signed-in H3 Ref2VA native sample

After the stereo/upscale lease was withdrawn, a coordinated Pinokio restart
released retained GPU model memory and restored both the current local
`/health`/`/ready` and stable Cloudflare `/health`/`/ready` to 200. The
launcher cleared its exact public restart-status generation. A separate
coherent six-hour `maestro-local` lease
(`maestro-h3-ref2va-live-01a0a212-20260923`) was validated and monitored
during the signed-in generation on source revision `980bc06`.

The owner test project submitted the benchmark's deterministic 608x352
red-circle/yellow-triangle reference PNG (SHA-256
`820ebc22b7fb2896dd9c0b2485e8e9d42ac567a5b838f3a665fafb1380547cab`)
and matching synthetic identity-and-motion brief. The private one-segment
request recorded `minimax_h3_ref2va`, 124 frames at 608x352, 20 steps,
Dense SDPA, seed `314159265`, one image reference, and no LoRAs or Turbo.
Job `78ec4edd8082415baf7dc0bd7ab0d364` completed in 81 seconds. Its
5.166667-second, 24 fps 608x352 HEVC/32 kHz stereo AAC output has SHA-256
`62d504183deea9bd381c5d12b33823e4dbbf31d2d80e445d3203a6d07231876d`,
matching the sealed sidecar; audio is finite and non-silent (RMS 0.016,
peak 0.759). The red circle and yellow triangle are detected in all 124
decoded frames, and the red object's horizontal centroid moves about 136
pixels right from first to last. Three sampled frames visually retain the
reference identity. The signed-in Gallery rose to 25 items, the queue
returned idle, and stable `/ready` remained 200.

The runtime log identified the loaded Ref2VA checkpoint by basename
`minimax_h3_ref2va_pruned_fp8_scaled.safetensors`; its 20,958,205,608 bytes
hash to `f86f2f79ebd2d76eb8eeb46091e83982e6ff51d255747e7b16e92834b392b8e9`.
The loaded Qwen3-VL-32B NVFP4-AWQ conditioner hashes to
`35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6`,
video VAE to `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522`,
and audio VAE to `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48`.
Two more private signed-in Ref2VA runs used only the managed `h3_turbo_v4`
asset, Dense SDPA, the same reference/seed/resolution, and no stacked LoRA.
Turbo8 job `67523e66b8054124a9f32886eab94e8a` completed in 66 seconds;
its 141-frame, 5.875-second HEVC/stereo AAC output hashes to
`2e5ac5159c455fec8b34fa5f065769b1dd05593a2c36fcc80e4de15e2203e749`.
Decoded audio was finite and non-silent (RMS 0.058, peak 0.693). Red and
yellow reference features were detected in all 141 frames; the red centroid
moved about 116 pixels right. Turbo4 job
`c9fc7859599744abb0d552758cb5b5fa` completed in 29 seconds with the
same 141-frame AV geometry; SHA-256
`6fc8b85d04f181dddf939338e6ae1cad9740527b0e96103f47f59dd6a00ec510`
matches its sidecar. Audio was finite and non-silent (RMS 0.068, peak 0.800),
and both reference-color features appeared in all frames while the red
centroid moved about 105 pixels right. Sampled frames of both clips visibly
retained the synthetic character. Turbo4's progress showed eight master
evaluations for four authored video steps, as designed by its dual-clock
schedule; that display is not evidence of eight video denoising steps.
After both jobs, Gallery held 27 items, queue was idle, and stable `/ready`
returned 200. The duration label correctly showed the aligned 5.88-second
output, but the browser range input's one-second step reported 5.46 seconds
for the same selection. The subsequent UI fix uses native 17-frame steps for
single-pass H3 durations and frame-level steps for long authored timelines.
After a production UI rebuild and stable-share reload, one keyboard step moved
the control from 5.17 to 5.88 seconds; the accessible slider value, visible
label, and expected 141 frames agreed. This checks the control, not a fresh
generation under the rebuilt UI.

An additional private **Dasiwa on Installed Ref2VA (Unverified)** sample used
the same reference, prompt, seed, 124-frame 608x352 geometry, four steps, and
Dense SDPA. Its sealed request contained only
`dasiwa_ref2va_hybrid_v1_4step.safetensors` at weight 1.0 (local SHA-256
`d2a9a723d97520232f17b6fec33335f9e94b03b2c67b56f91f16780355479274`),
with no managed Turbo or other LoRA. Job
`8c3f86e58e6647eb87ffe8c18084b26b` finished in 59.2 seconds. The
5.166667-second HEVC/32 kHz stereo AAC artifact hashes to
`679d950c02df5d9204bcac95186efb4faec8bbce122b8dcbd668fb185fc0f2df`,
matching its sealed sidecar. The red circle and yellow triangle are detected
in all 124 frames; sampled frames visibly keep the character, and the red
centroid moves about 52 pixels right. The decoded audio is finite but
effectively silent (RMS `0.000002`, peak `0.000108`); this prompt did not
explicitly request a sound. Gallery rose to 28 items, queue returned idle,
and stable `/ready` remained 200.

A controlled follow-up added only “Soft electronic footsteps are clearly
audible throughout.” to that brief. Dasiwa job
`bcbf780f6cd94a068d2c52d21b87a276` completed in 18.8 seconds with the
same checkpoint, reference, seed, LoRA, SDPA, four steps, and 124-frame
geometry. Its private 608x352 HEVC/32 kHz stereo AAC artifact hashes to
`f87ae2c3b156471a410170a3f8b9b80313689a8c64fbc034884d81765793942e`,
matching the sealed sidecar. Audio is finite and non-silent (RMS 0.0108,
peak 0.223; left/right difference RMS 0.0043). Red and yellow reference
features survive in all 124 decoded frames; the red centroid moves about 66
pixels right. This shows the installed-base profile can produce non-silent
stereo audio when prompted, while the first run's silence remains a valid
observed outcome rather than a demonstrated runtime defect. It supports
runtime tensor/shape compatibility and coherent synthetic A/V on the
installed checkpoint, **not** the author's exact-base contract or owner
audio/visual quality acceptance. Keep this profile explicitly unverified.

Removing the reference after applying Dasiwa exposed a UI-only estimate
failure: the Ref2VA profile left a legacy shared LoRA selection, and the
subsequent FL2VA estimate returned HTTP 400 when its separate FL2VA list was
unset. Applying an H3 profile now seals both architecture lists, preserving
any explicit opposite selection and using an empty list where appropriate.
A focused state regression passes, and a signed-in stable-share check applied
Dasiwa, removed the reference, then selected Quality and High without an
estimate error. A second issue was visible on the following FL2VA output:
its request sidecar retained the legacy shared Dasiwa name even though the
explicit FL2VA list was empty and the worker reported no active LoRAs. The
Gallery now derives its LoRA badges from the effective H3 architecture list;
the signed-in stable-share Gallery no longer mislabels that output. The
legacy field remains in its sealed request for repeat-setting compatibility.

The native and managed Turbo runs accept Ref2VA/SDPA and Turbo4/8 execution
with synthetic visual identity. The local loose checkpoint has no provenance
sidecar binding its bytes to the handler's declared source revision, so
alias/source-revision acceptance remains open, along with broader quality,
restart/crash recovery, and owner visual or listening acceptance.

### 2026-09-23 fresh signed-in H3 Base FL2VA native sample

The same signed-in owner project submitted the fixed procedural red-robot
brief through stable Cloudflare with private output, 608x352, 124 frames,
20 Dense SDPA steps, seed `314159265`, and an explicit empty FL2VA LoRA list.
Job `8ee5433a1943463d8c9a0f9d142774f1` completed in 74 seconds. The
worker loaded `minimax_h3_fl2va_pruned_fp8_scaled.safetensors` and logged
`No LoRAs activated for this generation (model_type=minimax_h3)`; the stale
legacy shared Dasiwa field in the sidecar did not select an adapter for this
segment. The loose 20,958,205,608-byte FL2VA checkpoint hashes to
`12944c1f7791637e7de12208aef04da82bd26b95271b1b47d817364315ade993`;
its source revision is not bound by a local provenance sidecar. Its private
HEVC/32 kHz stereo AAC output is 608x352 at 24 fps,
124 decoded frames and 5.166667 seconds. SHA-256
`98eb4366cae11a83e8010697ac74875b703159d3762cbb7ceba41fb8864644e6`
matches the sealed sidecar. Audio samples are finite and non-silent (RMS
0.0779, peak 0.229; stereo difference RMS 0.0171). Red and yellow features
were detected in all 124 decoded frames; the red centroid moved about 197
pixels right. Five sampled frames visibly retain the robot and emblem.
Gallery reached 30 items, the queue returned idle, and stable `/ready`
remained 200. This closes one live native Base/SDPA sample.

The same private brief also completed as high native Sol job
`b33bd3d23c40402bb21bb4251c9c21e5` at 1344x768, 124 frames, 20 steps,
seed `314159265`, Sol-Attn with tau 1.0, ten dense warm-up steps, two dense
blocks, and no LoRAs. Its 462-second run produced 5.166667 seconds of
1344x768/24 fps HEVC with 32 kHz stereo AAC. Output SHA-256
`71adb24916e1b98bfab9d85dcf7ee3282ee783aceedf39bf0dc5bc7f06d6d0df`
matches the sidecar. Decoded audio is finite and non-silent (RMS 0.118,
peak 0.583); red and yellow features appear in all 124 frames. Five sampled
frames keep the robot and emblem and show a leg-motion cycle. The red
centroid remains near x=659 and x=654 from first to last frame: the requested
left-to-right traverse is not visible in frame coordinates, although the
prompt also asked for a lateral camera move. This is a successful exact Sol
execution and playable A/V sample, with directional-motion adherence still
requiring a clearer visual result. Gallery reached 31 items and the queue
returned idle.

### 2026-09-23 signed-in H3 Base FL2VA Dense Turbo samples

The installed SageAttention2++ validation does not match its source build,
so Draft and Fast remain unavailable. Two explicit Turbo 4/8 (Dense) profiles
use the same managed `h3_turbo_v4` asset with Dense SDPA at 608x352. After a
coordinated Pinokio restart, both profiles appeared on the signed-in stable
Cloudflare page. The local and stable `/health` and `/ready` endpoints returned
200, and the launcher cleared its exact restart-status generation. Continuum
remained running.

The same private procedural brief and seed `314159265` completed as 4-step job
`5620b05702b24dad92101962535a0d6f` and 8-step job
`fe8e013537db42f9af97bf5bd3a2bb06`, with empty FL2VA LoRA lists. The
worker log confirms the managed Turbo LoRA was loaded for the four-step job;
the sealed requests specify `h3_turbo_v4` and `sdpa` for both.
The four-step Turbo schedule deliberately uses eight paired transformer/audio
ticks while advancing video on four of them; the worker's `8/8` progress is
therefore consistent with the authored four video steps.
The outputs are
608x352 HEVC with 124 frames at 24 fps and 5.166667 seconds, plus 32 kHz
stereo AAC. Their SHA-256 values match the respective sealed sidecars:
`55085f3360b3aa2db817cc8810cab692f95dd42862f64d775088ef2ce23a5dee`
and `cea885d5bc29b9e696839b4881456e98e750f56de6e49eb0444ef6bb99c60b0c`.
Both audio streams are finite and non-silent: RMS 0.0868/0.0902, peak
0.834/0.806, and stereo-difference RMS 0.0068/0.0043. Red and yellow
features persist in all 124 decoded frames, and the red centroids move about
234/230 pixels right. Four-frame contact sheets show a recognizable robot,
yellow chest emblem, and changing leg positions; the eight-step result has
simpler detail than the four-step result in this sample. The four-step job
recorded 110 seconds including a cold load, while the warm eight-step job
recorded 25 seconds. Gallery reached 33 items, the queue returned idle, and
stable `/ready` remained healthy. This closes one live Base managed Turbo4/8
matrix pass, not checkpoint source-revision proof or owner quality acceptance.
The first Turbo 4 profile application briefly displayed “Could not estimate
H3 performance” before submission. A duration change returned a successful
estimate, and a later Turbo 8 → Turbo 4 switch also estimated successfully.
No persistent admission failure reproduced; the initial estimate response was
not captured, so its cause remains unproven.

### 2026-08-27 PinkCherry beta-0.6 runtime receipt

- Source revision: `5a30ce7`; Linux `7.0.0-30-generic`; RTX 5090; NVIDIA
  `595.84`; CUDA compiler `12.8.93`.
- Producer: `minimax_h3_pinkcherry_fl2va`; checkpoint
  `PinkCherry_fl2va_MiniMax_H3_pruned_int8_convrot-beta-0.6.safetensors`
  at pinned revision `8642ce26b8ff3d671fb8370de70d8fd1b36b070c`, SHA-256
  `0cb2812f061003d9f345186d58f1bafbf902c6ad2b4c064590b4fc4811634ad1`;
  Heretic INT8 conditioner; no LoRA, managed Turbo, TeaCache, or model fallback.
- One private member-project job completed 28 Sol-Attn steps and produced a
  7,661,687-byte HEVC MP4 with 124 frames at 1344x768/24 fps plus 32 kHz stereo
  AAC. Output SHA-256:
  `d36103f99d9579637ef9bf35e17f7548a5368cba27c490e36a1782dd471054fd`.
- Observed peak VRAM was 27,333 MiB. Sampled frames retained one coherent adult
  performer, dress, stage, lighting, and plausible anatomy/motion; the audio
  stream was finite and non-silent (peak -19.34 dBFS, RMS -32.80 dBFS).
- Evidence class: live local GPU generation plus agent visual and technical A/V
  review, followed by direct human review. The owner accepted the clip as a
  generally acceptable output while explicitly noting that it is not a
  high-quality exemplar. The authorized generic account satisfies this bounded
  acceptance; do not rerun merely to reproduce it under a differently named
  owner account. PinkCherry beta-0.6 therefore passes this matrix row with a
  quality reservation, without becoming a default or preferred mature model.

### 2026-08-27 Ref2VA adapter runtime receipts

- Source revision: `d4685e6`; Linux `7.0.0-30-generic`; RTX 5090; NVIDIA
  `595.84`; CUDA compiler `12.8.93`. Both jobs used the installed Ref2VA
  checkpoint `minimax_h3_ref2va_pruned_fp8_scaled.safetensors`, SHA-256
  `f86f2f79ebd2d76eb8eeb46091e83982e6ff51d255747e7b16e92834b392b8e9`,
  with the same private procedural red-circle/yellow-triangle reference and
  seed `314159265`.
- Dasiwa job `40d504963e6646259a6888acd725ed44` used only
  `dasiwa_ref2va_hybrid_v1_4step.safetensors` at strength 1.0, four SDPA
  steps, and 608x352. It produced a 669,844-byte HEVC MP4 with 124 frames at
  24 fps and finite 32 kHz stereo AAC; output SHA-256
  `c372eb35f12c98fe4c57952e1ad03d0c45280b6ab2e1f6bea955ce0690f67b54`.
  Observed peak VRAM was 25,168 MiB. Sampled frames preserved the reference
  geometry, colors, emblem, and a stable simplified walk. This is useful
  coherence evidence on the installed suspected-compatible base, not proof of
  the unavailable exact Dasiwa base.
- Better Motion job `5f80f28b180d4b969d41225ee93afde0` used only
  `h3_Better_NSFW_Motion_V1.safetensors` at strength 0.9, 28 Sol-Attn steps,
  and 1344x768. It produced a 3,611,105-byte HEVC MP4 with 124 frames at 24
  fps and finite 32 kHz stereo AAC; output SHA-256
  `202efe5b0d56c3667a49be6675b04a4ba462a9e61ba70d3b897c96c1afe93726`.
  Aggregate observed peak VRAM was 28,003 MiB. Sampled frames preserved the
  reference identity while adding materially stronger 3D form, foot planting,
  and weight transfer than the Dasiwa probe.
- Both jobs completed without checkpoint, LoRA, stack, fallback, CUDA, OOM,
  output, or finality errors. Maestro fetched two pinned, sub-megabyte Ref2VA
  LoRA compatibility maps during the first preparation; no model artifact was
  downloaded. Evidence class is live local GPU generation plus agent visual
  and technical A/V review, followed by direct human comparison. The owner
  judged Better Motion materially better than Dasiwa in this sample. That
  promotes Better Motion to the leading observed Ref2VA motion experiment for
  the next private/mature evaluation, while leaving it opt-in; this neutral
  robot sample still does not establish mature-content quality across prompts.

### 2026-09-04 FlashVSR delivery row (1080p, Ultra, 4K)

- Host: Linux `7.0.0-30-generic`; RTX 5090; NVIDIA `595.84`; live Continuum
  runtime `env-rtx50` with PyTorch `2.10.0+cu130` / CUDA 13.0. Cases used the
  synthetic H3 benchmark payload, private output, and SDPA except 1080p
  (High native Sol 20). Native canvas was 1344x768 except the 608 Turbo
  probes used only while diagnosing delivery. Final publication selected the
  explicit delivery output; protected natives were not exposed; 4K is
  learned upscale, not a native-4K claim.
- Probe-cache bug: `probe_video_stream_metadata` and `get_video_info` cached
  on path only. FlashVSR wrote a 2688x1536 or 4032x2304 mux, then replaced
  the work path in place; dest probes kept the native 1344x768 hit and
  `upscale_exact` refused. The cache key now includes file identity
  (dev/inode/mtime/size). Model-free unit tests cover same-path replace.
- `base_1080p_delivery` (`flashvsr1.5`, `center_crop`, 1920x1080) completed
  **before** the cache-identity fix: valid 1920x1080, 124 frames, 24 fps,
  5.167s, 32 kHz stereo audio, sampled motion and non-black; 4,200,528
  bytes; SHA-256
  `4ab47d430ddb943de70b74bf0204c4fb310bde1811281c0ed4398a8362702218`;
  wall 521s. Geometry passed via coded-size center-crop from a dest that
  still probed native.
- `base_ultra_delivery` (`flashvsr2pass2`, `upscale_exact`, 2688x1536)
  completed **after** the cache-identity fix: dest after replace probed
  2688x1536; valid 2688x1536, 124 frames, 24 fps, 5.167s, 32 kHz stereo
  audio, sampled motion and non-black; 6,649,455 bytes; SHA-256
  `621a049badc45fec13b7ebe3222a24099348f1f7e5c60a5dbbe5f3f9c92e645a`;
  wall 1093s; public finality valid.
- `base_4k_delivery` (`flashvsr3`, `center_crop`, 3840x2160) completed after
  the same fix: FlashVSR encoded 4032x2304, then center-crop to 3840x2160;
  valid 3840x2160, 124 frames, 24 fps, 5.167s, 32 kHz stereo audio, sampled
  motion and non-black; 14,285,748 bytes; SHA-256
  `03e78558823fed16ff9327056f2f165db69734bfd6a91c78e9d22fd954f014bb`;
  wall 1480s; `native_4k` false; public finality valid.
- Evidence class: live local GPU generation plus automated validity and
  public-finality probes. This is not owner visual acceptance of
  detail/identity. Media remains in the private output store, not Git.
- Residual: SageAttention2++ is not importable in `env-rtx50`, so that row
  was not run (an SDPA fallback would not count). The first Ref2VA Turbo-4
  named case was rejected at generate with HTTP 404 after a successful
  private upload; that is request admission, not GPU failure. Uncommitted
  mixed `app/launch.py` still holds transactional H3 delivery breadcrumbs
  alongside unrelated dirty work and was not part of this commit.

When all applicable rows pass, update this matrix rather than deleting it. Keep
failed or intentionally unsupported variants as provenance, and remove only a
superseded command after its replacement is both documented and accepted.

## W4A8 runtime prerequisite receipt — 2026-09-07 UTC

Linux RTX 5090 / SM 12.0 passed the 256-by-256 synthetic W4A8 validation with
Torch 2.10.0+cu130 and Triton 3.6.0. Dispatch was eager weight quantization
and Triton W4A8 linear; relative MAE was 0.07095864661654136. The runtime
revision is `b812819a97ac11d01f4a3a16ba47dd38de3b2519`, and the schema-2
marker binds package digest
`2028f87be20ad79158b47895280fdc4ecf1491d7c010bfd4058cabf89e2b778b`.

The prior generic package lacked W4A8 APIs. The pinned local wheel replaced it
under a validated coordinator lease with rollback preserved. Both the initial
failed check and successful repair lease were withdrawn and confirmed cancelled.
Task-private grant, dispatch, marker, and withdrawal receipts are retained in
`.artifacts-temp/astra-w4a8-repair-20260907/`; installer CPU evidence is in
`.artifacts-temp/astra-w4a8-installer-20260907/`.

This satisfies only the small runtime prerequisite. The H3 W4A8 row remains
open for checkpoint-bound generation, playable output, fallback checks, and
visual/human acceptance. Native Windows runtime execution remains unverified.

### 2026-09-23 signed-in W4A8 live failure and repair attempt

On Linux RTX 5090 with the current `app/env-rtx50` runtime and source
`141d82f707e3754ba2f5ba661c1587cb4ce96729`, the fresh synthetic
W4A8 prerequisite passed (finite output, relative MAE 0.0710). The first
signed-in stable-share Video submission selected W4A8 in Advanced but adaptive
conditioning still submitted Base FL2VA. It was stopped before generated
media; this was a UI model-routing defect, not W4A8 execution evidence.

After the local UI correction, a new 608×352, 5.17-second, eight-step Dense
SDPA/Turbo submission loaded the intended
`minimax_h3_w4a8_fl2va` checkpoint (Kijai mixed W4A8 asset pinned by
`app/defaults/minimax_h3_w4a8_fl2va.json`). It failed before denoising step
0/8. Comfy-Kitchen rejected `w4a8_int8_linear` because MMGP had converted
the checkpoint's FP32 `weight_s_channel` to BF16; both Triton and eager
backends require FP32. The failed job was visible in the project queue before
the coordinated restart.
Local health and stable-share access remained ready. The failed attempt is
retained as negative evidence; the parameter-level MMGP dtype repair, its
small-load regression, and the fresh live retest are recorded below.

The repaired service then completed one fresh signed-in stable-share W4A8
generation on RTX 5090 (driver 595.84, SM 12.0; Torch 2.10.0+cu130,
Triton 3.6.0). The submitted sidecar records adaptive FL2VA preference and
producer model `minimax_h3_w4a8_fl2va`, seed 314159265, 608×352, 124 frames,
eight Dense SDPA steps, and no LoRAs. The backend loaded the pinned Kijai
W4A8 checkpoint, completed all eight denoising steps without a checkpoint
fallback, and recorded a 59-second generation. The Gallery showed the new
video tile through stable-share and made ranged media requests successfully.
The MP4 is 5.166667 seconds, HEVC 24 fps with stereo AAC 32 kHz; FFmpeg
decoded both streams without error. Its SHA-256 is
`31986669688616241cf6b4084907204d7582b6823613bd54bd7dbfdf99c22ff9`.
Three inspected frames show the same red robot and yellow triangle through
the requested turn and wave. Audio is non-silent (mean -25.5 dBFS, peak
-2.3 dBFS). This is live GPU, browser-Gallery, and agent visual/technical
media evidence. Browser Play and owner listening/visual acceptance remain
unverified; the W4A8 row is still open for those judgments, fallback checks,
and any required parity retest.

On source `b1403c4`, a second signed-in stable-share W4A8 Turbo 8/Dense SDPA
request used an authored two-beat, 15.79-second brief and fixed seed
`314159265`. The plan contained two FL2VA segments, 192 generated frames
each, with 379 published frames and no model switch. The current six-hour
`maestro-local` coordinator grant was validated before submission and at
eight-second intervals during execution. Both segments completed eight
denoising steps using the pinned W4A8 checkpoint; the worker logged no active
LoRAs or checkpoint fallback. The joined private Gallery output has SHA-256
`535bafba788b26edafce0dbdf8a86bc8002694fad186c82f43abce6eb68519f2`,
matching its sidecar. FFprobe found 379 H.264 frames at 608×352/24 fps,
15.791667 seconds of video, and 15.792 seconds of stereo 32 kHz AAC. FFmpeg
decoded both streams without errors; audio measured mean -22.6 dBFS and peak
-2.0 dBFS. Sampled beginning, post-join, and ending frames retain the red
robot and yellow chest emblem without an obvious identity break. The requested
turn and wave are not clearly visible in those samples, so prompt adherence
remains partial. The signed-in queue returned idle and the joined video
appeared in Gallery through stable-share. An approval click near the automatic
plan deadline briefly displayed a stale-review conflict while the server
auto-approved and completed the job; that confusing UI race remains open.
Owner visual and listening acceptance remains open.

### 2026-09-23 SageAttention2++ candidate kernel check

On the same validated six-hour `maestro-local` lease, the installed official
SageAttention 2.2.0 source build reported available on Linux SM120 but its
release-bound validation record did not match the installed distribution.
One direct BF16 NHD 1×128×16×128 kernel call returned finite output with
mean absolute difference 0.00395 from PyTorch SDPA and no recorded fallback
or error. This is a kernel smoke check only. The signed-in UI keeps the curated
Draft/Fast Sage profiles disabled because the release-bound validation record
does not match this installed source build. Advanced Settings still permits an
explicit Sage2 choice for a bounded candidate run.

Through the signed-in stable-share Studio, six subsequent Base FL2VA jobs
used the same synthetic robot brief, seed `314159265`, 124 frames at 24 fps,
no references or extra LoRAs, and produced Gallery-visible HEVC/AAC outputs:

| Case | Settings | Job | Runtime field | Output SHA-256 |
| --- | --- | --- | ---: | --- |
| `base_native_sage2` | 608×352, 20 steps, Sage2, no Turbo | `40b53bd75a844f5185267906ddb5dcb5` | 63 s | `cd36cddb67c59e3f99d59a87387008b8793bfe330f04874985e8990ee4bf9ece` |
| `base_fast_864_turbo_8_sdpa` | 864×480, 8 steps, Turbo V4, dense SDPA | `ed076e0c2fb3476286b9a1d81c2a6ad3` | 71 s | `ea2fb1458807eec91afd85a1e9fce5b6620f31fa5f603bc61145a1cd93461ec9` |
| `base_fast_864_turbo_8_sage2` | 864×480, 8 steps, Turbo V4, Sage2 | `2a33dd9fa25c4e0c8276916f604dd1b9` | 38 s | `9af7fcb1a6bb4821637e8ba2d87308698b4a9a316dc53cef418a3ddcaa745c44` |
| `base_turbo_4_sdpa` | 608×352, 4 steps, Turbo V4, dense SDPA | `3a54ca9b64aa4abd9af3946b3c5926d2` | 48 s | `3711ba3fdd6f174760b8d05068da29d07854acb66a69c42284e6e1fd2d0b6315` |
| `base_turbo_4_sage2` | 608×352, 4 steps, Turbo V4, Sage2 | `93bdd229fe9f455d87f060dad54df9d4` | 22 s | `5b3ce27501fd50c11026f966dc4e0931fa010f06790a7c92bf94af15d7d8a8f2` |
| `base_turbo_8_sage2` | 608×352, 8 steps, Turbo V4, Sage2 | `940e3eed18d14d7eacebc8dfcab476dc` | 22 s | `0bf47320fc306bc81c2cbb3dbda179a3360fcc2da740d6291d14d38366ecd974` |

Each sidecar records the stated model, seed, schedule, attention engine, and
absence of other LoRAs. The first three outputs match their sidecar media
hashes, decode all 124 video frames and their audio streams without error,
and last 5.166667 s. Sampled middle frames show a coherent red walking robot
against the requested pale blue background in both 864×480 Turbo outputs.
The yellow triangle is visible on the robot's side rather than clearly on its
chest, so prompt adherence is partial. The native sample is also coherent in
sampled frames. Audio is present and non-silent in all three, but owner
listening remains open.

The later 608×352 samples used the same seed and brief. Their sidecars and
media hashes also match; the three new files each decode 124 frames with a
non-silent audio stream. Middle frames of both Sage2 outputs show a coherent
walking robot with the yellow emblem. Whole-video, audio-quality and owner
review remain open. Full-frame video SSIM against same-seed dense outputs was
0.857 at 608×352 Turbo 4, 0.881 at 608×352 Turbo 8 (using the earlier dense
sample above), and 0.906 for the 864×480 Turbo 8 pair. These measurements
describe similarity to dense outputs, not aesthetic quality or fidelity to
the prompt.

After the native job the process reported 1,000 Sage2 calls, zero Sage2
fallbacks, and zero errors; after the 864×480 matched Turbo job it reported
1,400 calls, and after the 608×352 Turbo 4/8 samples 2,200, with the same zero
fallback/error counts. The intervening dense job did not increment the Sage2
count. This proves candidate Base FL2VA kernel execution on this host,
including both Turbo schedules. The sidecar runtime fields do not isolate
model load, cache state, or other run conditions, so they are not a validated
speedup. Do not refresh the release validation record or enable curated Sage
profiles from these samples alone. The current record requires two samples
each for the 608×352 Turbo 4/8 cases, at least 3,100 kernel calls, explicit
human visual/audio review, and exact source/runtime/model binding. Those gates
and other H3 checkpoint families remain open.

With the queue idle, the local-only release control returned 200 and
`released: ["generation model"]`; system telemetry then reported the H3
generation model `loaded: false`. Local and stable-share `/health` and `/ready`
remained 200. The task's lease observer stopped, the exact GPU grant was
withdrawn and its outbox became `denied`. Continuum and stable Cloudflare
access stayed running without a resident generation model. This accepts the
loaded-generation-model unload path on this host, distinct from the earlier
loaded-LLM unload check and from release during a concurrent generation.

### 2026-09-23 idle model-release recovery

The live **Release models** API returned 409 with an empty visible queue both
before and after a restart. A bounded diagnostic on the restarted process
identified its first guard: durable H3 jobs held for legal-access recovery and
a Director recovery entry remained `queued` in other projects. They were not
generating, but the endpoint rejected every queued record before trying its
generation and native-GPU locks. The guard now rejects `running` jobs while
those locks continue to serialize a queued worker that starts concurrently.
A focused race test made a queued worker attempt admission during unload and
confirmed that model work waited. After the coordinated restart, local and
stable-share health/readiness returned 200 and the same idle API request
returned 200 with `released: []`. This proves the false 409 is gone with the
held records still present. A subsequent signed-in stable-share local Gemma 4
31B chat returned the exact requested marker. With that LLM resident, the
same release API returned 200 with `released: ["LLM"]`; the local llama-server
process exited, while local and stable-share readiness stayed 200. This accepts
one loaded-LLM release path. A generation model was not resident in these
requests, so its unload path remains a separate live check.

## NVFP4 scale-layout check — 2026-09-07 UTC

The CPU fallback now matches the eager reference for padded physical scale
tiles at logical widths 32/64/96, both nibble layouts, and FP32/BF16/FP16.
This is separate from native kernel acceptance.

A coordinator-authorized synthetic LightX2V run on RTX 5090 (SM 12.0),
Torch 2.10.0+cu130 reached native quantization and GEMM, then failed with
`cuBLAS error: 7` on its first case: logical M=1, N=128, K=64, with M padded
to 128. Zero numerical cases completed. The process exited and the lease was
withdrawn and confirmed cancelled. No fallback or retry was attempted.

Receipts: `.artifacts-temp/astra-nvfp4-scale-20260907/`. Native acceptance
remains open under task-local `NVFP4-LIGHTX-SHAPES`: inspect the pinned kernel's
shape and runtime contract before the next bounded lease. No full-model or
quality conclusion follows from this attempt.

## LightX2V route comparison — 2026-09-07 UTC

A fresh lease ran the same nine small synthetic cases with the existing
process-only `LIGHTX2V_NVFP4_GEMM=cutlass` selector: K=32/64/96, M=1/50/129,
N=128. All outputs were finite and correctly shaped; relative MAE against the
dequantized reference ranged from 0.00694 to 0.05556. Peak allocated tensor
memory was 8,785,920 bytes. The process exited, its selector expired, and the
lease was confirmed cancelled. Default routing was not changed or promoted.

The default cuBLAS failure remains separate. ELF/loader inspection resolves the
extension's required `libcublasLt.so.12` to CUDA 12.0, which lacks the FP4
scale-mode attributes used by the inspected source. The exact failing call is
still uninstrumented. A subsequent separate lease exposed an already-installed,
RECORD-verified cuBLASLt 12.8.3.14 library through a child-only loader path.
Process maps confirmed that library, but the first case then failed with
`Unable to find suitable cuBLAS GEMM algorithm`; zero numerical cases completed.
Its lease is confirmed cancelled and the task-private link was removed.

The instrumented CUDA-13 diagnostic below completes that bounded build check.
The complete private package build and resolved-dependency check subsequently
passed as recorded below. Packaging, rollback, and installed-runtime
verification remain required before promotion. The passing CUTLASS comparison is not promoted to a permanent
default. Receipts: `.artifacts-temp/astra-lightx-route-20260907/` and
`.artifacts-temp/astra-lightx-cublas128-20260907/`. This evidence does not
establish full-model correctness, speed, or Windows acceptance.

## CUDA-13 cuBLAS diagnostic — 2026-09-07 UTC

A fresh validated lease compiled the source-bound cuBLAS path with CUDA 13.0
and the selected environment's libraries. The diagnostic ELF requires
`libcublasLt.so.13` and `libcudart.so.13`, with neither CUDA-12 dependency;
process maps confirmed the CUDA-13 library resolution. It used an independent
Torch operator and did not replace the installed package.

The exact previously failing physical M=128/N=128/K=64 case passed with finite
output and relative MAE 0.0069444444961845875. The diagnostic binary hash is
`dc0d700b1f712928414e4eef5d86bb2e1167f86a28fa17e2d9c332a08cf4e577`.
The process exited and the lease was withdrawn/confirmed cancelled. Receipts:
`.artifacts-temp/astra-lightx-cu13-build-20260907/`.

This diagnostic supports the ABI diagnosis. Diagnostic operators and temporary
selectors remain unpromoted.

## Complete CUDA-13 package — 2026-09-07 UTC

The source-bound complete private package built and passed all nine synthetic
default-cuBLAS cases under a fresh validated lease. Actual process maps used
CUDA-13 libraries with no CUDA-12 cuBLASLt/runtime mapped. The child exited
successfully and the lease is confirmed cancelled. See the
[complete build evidence and receipts](ASTRA_CONTINUATION_HANDOFF_2026-09-06.md#cuda-13-diagnostic-result--2026-09-07-utc)
for exact shapes, numerical results, source/binary hashes, and the corrected
compiler flags.

The following installed-runtime acceptance supersedes the earlier private-only
boundary. Normal launcher reproducibility, full-model generation, visual
quality, performance, and Windows acceptance remain open.

## Installed CUDA-13 LightX runtime — 2026-09-07 UTC

The verified wheel is installed in `app/env-rtx50`. A separate fresh lease
validated all nine synthetic cases through the installed package without a
private import path or library-path override. Installed payload hashes,
unique distribution metadata, native dispatch, and actual CUDA-13-only
library maps passed. The original package is preserved for exact rollback;
failure-path rollback and inventory checks have separate CPU/mock evidence.
Both processes exited and the lease is confirmed cancelled.

See the [installed-runtime receipts and remaining rollout requirements](ASTRA_CONTINUATION_HANDOFF_2026-09-06.md#installed-lightx-runtime--2026-09-07-utc).
No service restart occurred. The managed launcher integration below supersedes
the old Linux wheel path. This is
installed synthetic-kernel acceptance, not live-service adoption, full-model
quality, performance, DoRA, or Windows acceptance.

## Managed LightX source installer — 2026-09-07 UTC

The normal source installer built with the project-local CUDA 13/GCC 14
toolchain, installed its result, and passed all nine default-cuBLAS numerical
cases under a fresh validated lease. Fresh-process mappings prove use of the
selected CUDA-13 libraries. A second invocation reused the verified package
without creating another build attempt or changing its receipt. The bounded
client waiter exited, and withdrawal is confirmed.

Install, both Update branches, and standalone setup now reach that helper on
Linux CUDA 13. Source/launcher review and 107 applicable CPU tests (one existing
skip) support the wiring; native Pinokio UI execution was not performed.
See [managed build hashes, receipts, and remaining gates](ASTRA_CONTINUATION_HANDOFF_2026-09-06.md#managed-lightx-launcher-integration--2026-09-07-utc).
This closes the current Linux managed package/build check. It does not prove
live-service adoption, full-model generation, visual quality, performance,
DoRA correctness, or native Windows acceptance.

## MMGP DoRA package prerequisite — 2026-09-07 UTC

The reviewed `3.7.12+maestro1` package and real NVFP4 scale protocol are installed
in both local runtimes. Each passes 16 CPU numerical/dispatch tests with CUDA
confirmed uninitialized. The final wheel SHA256 is
`9e55e53d7ad975df7ab7d18dde82e0720b333a80a315fc189073af1fdd61c4cf`;
the installed offload source SHA256 is
`972451f19d3471bf96c47e241bffb1c6dca1a64e5fab3c75755fc5cd2fab5f15`.

The separate eight-case synthetic installed GPU check passed on RTX 5090,
Torch 2.10.0+cu130. Zero-effective DoRA retained native LightX quantization/GEMM
with zero reference error. Nonzero DoRA plus ordinary LoRA matched the pinned
BF16 rounding sequence bit-for-bit; relative MAE against the independent ideal
reference was 0.0001430–0.0002503. Both bias states and logical rows 1/50 passed with the legacy NVFP4 fixture
at width 64 and output width 128. Outputs were finite CUDA BF16 and packed
bytes were unchanged. Exact source/package hashes and actual CUDA-13 library
maps were verified. Peak allocated tensor memory was 42,388,480 bytes.

The child exited and the exact coordinator lease is confirmed cancelled.
Receipts: `.artifacts-temp/astra-mmgp-integration-20260907/`. This closes only
the synthetic installed DoRA boundary; application/model rows, full-model
quality, performance, Windows, and running-service adoption remain open.

## 2026-09-24 YuE2 joint-v9 decoder comparison

The installed Sound/Vision service reported the optional `joint-v9` decoder
available after validating the pinned tokenizer head and NAR adapter, plus the
configured YuE2-3B model architecture. The signed-in Maestro stable-share UI
showed the decoder choice with stock as the default. Its native worker merged
196 NAR LoRA projections and replaced four audio input/output tensors only
after semantic generation; artist/style LoRA stacking is still excluded.

One local Sound/Vision request, `maestro-joint-v9-probe-20260924-01`, rendered
take `c5c5a18f57134f9584994fa8c210926e` under a fresh, coherently validated
20-minute `sound-vision-local` coordinator grant. The grant was withdrawn after
the take succeeded, and the outbox no longer authorizes work. The control was
stock take `5df416d7a2d243c0b3cf1f3574750a05` with the same ABC, lyrics,
style, generation settings, and effective take seed `2577657602294637337`.
The stock request asked for an automatic seed; the comparison supplied its
resolved seed explicitly. Title and decoder choice also differed. Score SHA-256 was
`1591b895678a796a8f0d5b7af05fdedfcfd6e0d06c7abf42bfffea1c05b4dca5`;
the semantic token files were byte-identical, while latent and WAV files
differed. The v9 delivery records both pinned asset hashes and the merge count.

Both WAVs have 2,159,936 frames at 48 kHz stereo, or 44.9987 seconds, with no
reported warning, truncation, or clipped sample. Stock peak/RMS were
0.864787/0.138130; joint-v9 peak/RMS were 0.983242/0.147431. Their full-waveform
correlation was 0.949182 and difference RMS 0.046436. These are technical
measurements, not a preference or fidelity verdict. The score's nominal 17.14
seconds still does not match the rendered audio length. Human listening and
duration fidelity remain open before promoting v9 as a quality default.
