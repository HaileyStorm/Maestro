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
| Music3 Studio generation | Verify the published runtime with `python app/scripts/start_music3_runtime.py verify --pinokio-root "$PINOKIO_HOME"`, start it through the normal Pinokio flow, and generate one private lyrical song plus one instrumental song in Studio. | The exact pinned runtime/model generation is active, LAN/Cloudflare Music3 access remains off as intended, cancellation works, both outputs are playable with requested duration/style, lyric sections stay ordered, and the owner accepts musical and vocal quality. Runtime verification without a song does not close this row. |
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

Installed-runtime rollout, reproducible distribution packaging, rollback,
full-model generation, visual quality, performance, and Windows acceptance
remain open. The installed package and running services were not changed.
